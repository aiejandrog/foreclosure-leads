#!/usr/bin/env python
"""_lpchaintest.py — lp_refresh.py's exit contract, and where the freshness stamp sits.

WHY THIS EXISTS (2026-09-21/22). refresh-dealflow.bat reported `BAT_EXIT=7` -> `LP CHAIN exit 2`
on a night when the LIS PENDENS sweep had in fact worked: 46 paid 2Captcha solves, 408 Miami-Dade
filings folded in that had never been pulled before. But `lp_meta.json` still read
`ran: 2026-09-18 / newest_filing: 9/9/2026`, because the stamp was written at the BOTTOM of
lp_refresh.py — below a fast-lane skip-trace that exited 2 when the phone vendor rejected the call.
The chain stopped there and the stamp never got written.

Two readers in a row — the operator and a project session — then took a stale stamp as evidence
that the sweeper was dead, and went looking for a missing captcha.key and an empty 2Captcha
balance. Neither was the problem. The cost of that bug was a day of the wrong diagnosis, so the
two properties that prevent it are pinned here:

  1. the freshness stamp is written while the data it describes is final, i.e. after the board-row
     step and BEFORE anything that depends on a third party;
  2. a phone vendor's failure is benign for this chain, because the phones step is last and there
     is nothing after it to protect.

Run: python _lpchaintest.py      (no network, no data files, no captcha spend)
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, 'lp_refresh.py'), encoding='utf-8').read()
TREE = ast.parse(SRC)

R = []


def rec(name, ok, detail=''):
    R.append(bool(ok))
    print(('  PASS  ' if ok else '  FAIL  ') + name + (('   -- ' + str(detail)) if detail else ''))


def _main_body():
    for n in TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == 'main':
            return n.body
    return []


def _steps():
    """Every run(...) call in main(), in source order: (label, ok_codes, lineno)."""
    out = []
    for node in ast.walk(ast.Module(body=_main_body(), type_ignores=[])):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'run'):
            continue
        label = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else '?'
        ok = (0,)
        for kw in node.keywords:
            if kw.arg == 'ok':
                ok = tuple(e.value for e in kw.value.elts)
        out.append((label, ok, node.lineno))
    return sorted(out, key=lambda x: x[2])


def _call_lines(fname):
    """Line numbers of bare `fname()` calls inside main()."""
    return sorted(n.lineno for n in ast.walk(ast.Module(body=_main_body(), type_ignores=[]))
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fname)


STEPS = _steps()
LABELS = [s[0] for s in STEPS]

print('-- the chain still runs the steps it exists to order --')
rec('all eight chain steps are present', len(STEPS) >= 8, LABELS)
for want in ('SWEEP', 'RESOLVE (lp_resolve)', 'RESOLVE PASS 2', 'VALUE', 'CASE STATUS',
             'BOARD ROWS', 'PHONES'):
    rec('step present: %s' % want, any(want in l for l in LABELS))

# The ordering bug this chain was written to prevent (lp_resolve's whole-row merge strips
# lp_values' output). If these ever invert again, every LP lead ships value=0.
_i = {k: next((n for n, l in enumerate(LABELS) if k in l), -1)
      for k in ('RESOLVE (lp_resolve)', 'VALUE', 'BOARD ROWS', 'PHONES')}
rec('lp_resolve runs before lp_values', 0 <= _i['RESOLVE (lp_resolve)'] < _i['VALUE'], _i)
rec('lp_values runs before lp_leads', 0 <= _i['VALUE'] < _i['BOARD ROWS'], _i)
# The benign-vendor-code argument below rests on this: nothing that gathers data runs after the
# phones step, so a phone vendor's failure cannot leave a later step working on a half-built file.
# The only thing allowed below it is REBUILD, which is opt-in (--rebuild), local, and simply
# renders whatever leads_final.json already holds.
rec('nothing but the optional REBUILD runs after the phones step',
    all('REBUILD' in l for l in LABELS[_i['PHONES'] + 1:]), LABELS[_i['PHONES'] + 1:])
rec('REBUILD is opt-in', re.search(r'if a\.rebuild:\s*\n\s*run\(', SRC) is not None,
    'a default-on rebuild below the phones step would make its failure load-bearing again')

print('\n-- the freshness stamp records the DATA, not how far the script got --')
stamp = _call_lines('_stamp')
rec('_stamp() is called from main()', len(stamp) == 1, stamp)
if stamp:
    board = next((ln for l, _, ln in STEPS if 'BOARD ROWS' in l), 10 ** 9)
    phones = next((ln for l, _, ln in STEPS if 'PHONES' in l), -1)
    rec('the stamp is written AFTER the board rows exist', board < stamp[0],
        'stamping before lp_leads would date filings the board has not got')
    rec('the stamp is written BEFORE the phones step', stamp[0] < phones,
        'this is the 09-21 bug: a phone vendor outage below the stamp froze lp_meta.json at 09-18')

_stamp_src = SRC.split('def _stamp')[-1].split('\ndef ')[0]
rec('the stamp writes lp_meta.json', 'lp_meta.json' in _stamp_src)
rec("the stamp reads lis_pendens.json for its as-of date", 'lis_pendens.json' in _stamp_src)
rec('a failed stamp cannot kill the run', 'except Exception' in _stamp_src,
    'it is a record of the run, not a step of it')

print('\n-- a phone vendor outage degrades the run, it does not stop it --')
ok_phones = next((ok for l, ok, _ in STEPS if 'PHONES' in l), ())
# skiptrace.py's documented non-crash exits: 2 provider rejected (TraceAborted), 3 provider down
# (MAX_STRIKES), 4 over --max-spend, 5 daily budget cap. All four mean "no phones tonight".
for code, why in ((2, 'provider rejected the call - balance dry or key expired'),
                  (3, 'provider looks down - aborted after MAX_STRIKES'),
                  (4, 'run would breach --max-spend, nothing traced'),
                  (5, 'shared daily budget cap reached')):
    rec('phones exit %d is benign (%s)' % (code, why), code in ok_phones, 'ok=%s' % (ok_phones,))
rec('phones exit 1 is NOT benign', 1 not in ok_phones,
    'skiptrace exits 1 for a missing key AND for any uncaught exception - '
    'swallowing it would swallow crashes')

print('\n-- a machine with no skip-trace key skips the step instead of dying on it --')
rec('_have_trace_key() exists', 'def _have_trace_key' in SRC)
rec('the phones step is guarded by it', re.search(r'if _have_trace_key\(\):\s*\n\s*run\(', SRC)
    is not None, 'a missing key is a precondition, not a failure')
rec('and the skip is still reported', re.search(r'else:\s*\n\s*DEGRADED\.append', SRC) is not None,
    'a silently skipped step is how this class of bug hides')

print('\n-- benign is reported, never swallowed --')
run_src = SRC.split('def run(')[-1].split('\ndef ')[0]
rec('a benign non-zero code lands in DEGRADED', 'DEGRADED.append' in run_src)
rec('a non-benign code stops the chain', 'sys.exit(r.returncode)' in run_src)
rec('CHAIN STOPPED names the step that stopped it', 'CHAIN STOPPED at' in run_src,
    'LPEXIT alone cannot say which of eight scripts spoke - the log line is the only thing that can')
rec('a degraded run exits 4', re.search(r'if DEGRADED:(?:.|\n)*?sys\.exit\(4\)', SRC) is not None)

print('\n-- the batch file describes the code it will actually see --')
BAT = open(os.path.join(HERE, 'refresh-dealflow.bat'), encoding='utf-8').read()
lp = BAT.split('[2c/5]')[-1].split('[2d/5]')[0]
rec('the LP stage reads an exit code', 'LPEXIT' in lp)
rec('and carries it to the run verdict', 'RUNEXIT=7' in lp)
rec('the legend does not claim the codes come from lis_pendens.py',
    'Codes from lis_pendens.py' not in lp,
    'they come from whichever of eight scripts stopped the chain')
rec('the legend points the reader at the CHAIN STOPPED line', 'CHAIN STOPPED' in lp,
    'that line names the script; no static legend can')
rec('the legend explains exit 4 as DEGRADED', re.search(r'4 = DEGRADED', lp) is not None)

print('\n%d passed, %d failed' % (sum(R), len(R) - sum(R)))
sys.exit(0 if all(R) else 1)
