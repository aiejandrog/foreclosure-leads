#!/usr/bin/env python3
"""_titlechaintest.py — F2: run_title_chain.py puts title discovery and owner tokens on the nightly
schedule, off by default, paid tokens behind a second switch and the shared monthly cap.

Nothing here touches the network or spends: the discovery and token workers are stand-ins that
record their argv. Case numbers, owners and dates are invented.

Run: python _titlechaintest.py
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time

import run_title_chain as T

D = datetime.date
FAIL = []


def check(name, cond, got=None):
    if not cond:
        FAIL.append(name)
        print('FAIL  %s  -> %r' % (name, got))
    else:
        print('ok    %s' % name)


TODAY = D(2026, 9, 26)
ROWS = [
    {'Case #': '2025-000201-CA-01', 'owner_clean': 'OWNER ONE', 'AuctionDate': '10/30/2026', 'county': 'MIAMI-DADE'},
    {'Case #': '2025-000202-CA-01', 'owner_clean': 'OWNER TWO', 'AuctionDate': '09/29/2026', 'county': 'MIAMI-DADE'},
    {'Case #': '2025-000203-CA-01', 'owner_clean': '', 'AuctionDate': '09/30/2026', 'county': 'MIAMI-DADE'},
    {'Case #': '2025-000204-CA-01', 'owner_clean': 'OWNER FOUR', 'AuctionDate': '09/20/2026', 'county': 'MIAMI-DADE'},
    {'Case #': '2025-000205-CA-01', 'owner_clean': 'OWNER FIVE', 'AuctionDate': '10/02/2026', 'county': 'MIAMI-DADE'},
    {'Case #': 'CACE-25-000206', 'owner_clean': 'OWNER SIX', 'AuctionDate': '09/28/2026', 'county': 'BROWARD'},
    {'Case #': '2025-000207-CA-01', 'owner_clean': 'OWNER SEVEN', 'AuctionDate': '12/15/2026', 'county': 'MIAMI-DADE'},
]
priv = tempfile.mkdtemp()
# a fresh report for 205 (2 days old) and a stale one for 201 (30 days old)
for case, age_days in (('2025-000205-CA-01', 2), ('2025-000201-CA-01', 30)):
    p = os.path.join(priv, case + '.json')
    open(p, 'w').write('{}')
    t = time.time() - age_days * 86400
    os.utime(p, (t, t))

# ---- plan
cases = T.plan(ROWS, priv, TODAY, limit=10)
check('plan: near-sale first, then later sales', cases == ['2025-000202-CA-01', '2025-000201-CA-01', '2025-000207-CA-01'], cases)
check('plan: ownerless case skipped', '2025-000203-CA-01' not in cases, cases)
check('plan: passed sale skipped', '2025-000204-CA-01' not in cases, cases)
check('plan: fresh report (2 days) skipped', '2025-000205-CA-01' not in cases, cases)
check('plan: old report (30 days) redone', '2025-000201-CA-01' in cases, cases)
check('plan: non-Miami case never planned', 'CACE-25-000206' not in cases, cases)
check('plan: limit honoured', T.plan(ROWS, priv, TODAY, limit=1) == ['2025-000202-CA-01'])
check('plan: conflicting duplicate lead rows refuse the file, plan nothing',
      T.plan(ROWS + [dict(ROWS[0], owner_clean='SOMEONE ELSE')], priv, TODAY, limit=10) == [])

# ---- off by default
calls = {'disc': [], 'tok': []}
def disc(argv): calls['disc'].append(argv); return 0
def tok(argv): calls['tok'].append(argv); return 0
rc = T.run([], env={}, today=TODAY, discover=disc, tokens=tok, remaining=lambda: 50.0, private_dir=priv, rows=ROWS)
check('off without DEALFLOW_TITLE: exit 0, nothing runs', rc == 0 and not calls['disc'] and not calls['tok'], calls)

# ---- on: discovery only (tokens need their own switch)
rc = T.run([], env={'DEALFLOW_TITLE': '1'}, today=TODAY, discover=disc, tokens=tok,
           remaining=lambda: 50.0, private_dir=priv, rows=ROWS)
check('on: exit 0', rc == 0, rc)
check('on: tokens stay off without DEALFLOW_TITLE_TOKENS', calls['tok'] == [], calls['tok'])
check('on: one discovery call per case, default limit 3', len(calls['disc']) == 3, calls['disc'])
a0 = calls['disc'][0]
check('discovery gets the case', a0[:2] == ['--case', '2025-000202-CA-01'], a0)
check('discovery gets its own nightly ledger, not the manual one',
      a0[a0.index('--captcha-ledger') + 1] == T.NIGHTLY_LEDGER != 'captcha-budget.json', a0)
check('discovery gets only the nominal caps', a0[a0.index('--captcha-max-spend') + 1] == '0.01'
      and a0[a0.index('--vision-max-spend') + 1] == '0.01', a0)
st = json.load(open(os.path.join(priv, 'nightly-status.json')))
check('status file: counts only', st['cases_done'] == 3 and st['tokens'] == 'off', st)
check('status file: no case numbers or names in it', not re.search(r'20\d\d-\d{6}|OWNER', json.dumps(st)), st)

# ---- tokens on, capped by the month
calls = {'disc': [], 'tok': []}
T.run([], env={'DEALFLOW_TITLE': '1', 'DEALFLOW_TITLE_TOKENS': '1'}, today=TODAY, discover=disc, tokens=tok,
      remaining=lambda: 50.0, private_dir=priv, rows=ROWS)
check('tokens on: run once, before discovery', len(calls['tok']) == 1, calls)
ta = calls['tok'][0] if calls['tok'] else []
check('tokens: budget = 10 x day of month (monthly state)', ta and ta[ta.index('--token-budget') + 1] == '260', ta)
check('tokens: per-month state file', ta and ta[ta.index('--state') + 1].endswith('owner-tokens-2026-09.json'), ta)
check('tokens: dollar cap is the requested $0.50', ta and ta[ta.index('--captcha-max-spend') + 1] == '0.5000', ta)
calls = {'disc': [], 'tok': []}
T.run([], env={'DEALFLOW_TITLE': '1', 'DEALFLOW_TITLE_TOKENS': '1'}, today=TODAY, discover=disc, tokens=tok,
      remaining=lambda: 0.12, private_dir=priv, rows=ROWS)
ta = calls['tok'][0] if calls['tok'] else []
check('tokens: cap cut to what the month has left', ta and ta[ta.index('--captcha-max-spend') + 1] == '0.1200', ta)
calls = {'disc': [], 'tok': []}
T.run([], env={'DEALFLOW_TITLE': '1', 'DEALFLOW_TITLE_TOKENS': '1'}, today=TODAY, discover=disc, tokens=tok,
      remaining=lambda: 0.0, private_dir=priv, rows=ROWS)
check('tokens: month spent -> no paid run, discovery still runs', calls['tok'] == [] and len(calls['disc']) == 3, calls)
st = json.load(open(os.path.join(priv, 'nightly-status.json')))
check('tokens: skip reason recorded', st['tokens'].startswith('skipped'), st)
args, _ = T.token_args(TODAY, priv, 'x.json', 10, 99.0, lambda: 50.0)
check('tokens: never above run_owner_tokens\' $1.50 ceiling', args[args.index('--captcha-max-spend') + 1] == '1.5000', args)

# ---- failures and the deadline
calls = {'disc': [], 'tok': []}
def bad(argv):
    calls['disc'].append(argv)
    if len(calls['disc']) == 1:
        raise RuntimeError('clerk down')
    if len(calls['disc']) == 2:
        raise SystemExit(2)
    return 0
rc = T.run(['--limit', '3'], env={'DEALFLOW_TITLE': '1'}, today=TODAY, discover=bad, private_dir=priv, rows=ROWS)
st = json.load(open(os.path.join(priv, 'nightly-status.json')))
check('one failing case does not stop the rest', rc == 0 and len(calls['disc']) == 3 and st['cases_failed'] == 2
      and st['cases_done'] == 1, st)
tick = iter([0, 0, 1000, 1000, 1000])
calls = {'disc': [], 'tok': []}
T.run(['--deadline', '10'], env={'DEALFLOW_TITLE': '1'}, today=TODAY, discover=disc, private_dir=priv,
      rows=ROWS, clock=lambda: next(tick))
st = json.load(open(os.path.join(priv, 'nightly-status.json')))
check('no new case after the deadline', len(calls['disc']) == 1 and st['stopped'] == 'deadline', (calls, st))
calls = {'disc': [], 'tok': []}
T.run(['--plan'], env={}, today=TODAY, discover=disc, tokens=tok, private_dir=priv, rows=ROWS)
check('--plan: no discovery, no tokens', calls == {'disc': [], 'tok': []}, calls)

# ---- the wiring
here = os.path.dirname(os.path.abspath(__file__))
bat = open(os.path.join(here, 'refresh-dealflow.bat'), encoding='utf-8').read()
m = re.search(r'^python -u run_title_chain\.py >> "%LOG%" 2>&1\s*$', bat, re.M)
check('bat: the stage line is there, unconditional (the script gates itself)', m is not None)
i2b, i2e, i3 = bat.find('echo [2b/5]'), bat.find('echo [2e/5] Title discovery'), bat.find('echo [3/5]')
check('bat: [2e/5] sits after the records step and before [3/5]', 0 < i2b < i2e < i3, (i2b, i2e, i3))
echo = re.search(r'^echo \[2e/5\].*$', bat, re.M).group(0)
check('bat: no parentheses in the echo', '(' not in echo and ')' not in echo, echo)
src = open(os.path.join(here, 'miami_title_discovery.py'), encoding='utf-8').read()
check('discovery: --captcha-ledger defaults to the old ledger file',
      "add_argument('--captcha-ledger', default='captcha-budget.json'" in src)
check('discovery: the ledger arg is reduced to a bare file name', "Path(args.captcha_ledger).name" in src)
rt = open(os.path.join(here, 'run_title_chain.py'), encoding='utf-8').read()
check('chain never imports a board, publish or send module',
      not re.search(r'import (send_server|cadence|publish|foreclosure_leads|outreach_email)', rt))

print()
if FAIL:
    print('%d FAILED: %s' % (len(FAIL), ', '.join(FAIL)))
    sys.exit(1)
print('all passed')
