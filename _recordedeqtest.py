"""Recorded-debt equity tier (2026-11-11, Alex: "absolutely go on"). Invented fixtures, no network.

A lender's filed case (LP, no judgment yet) whose recorded chain is PRICED gets
equity_state.card()['recorded'] with recorded_debt / recorded_net. It must:
  * never make a figure sayable (may_quote stays False, net stays None);
  * reach Call Mode's Has equity lane only when value less recorded debt is above zero;
  * say "recorded debt only ... do not quote a number" on the handset, never "Net equity";
  * leave the diligence hold (UNDERWATER and every other flag) exactly as it was.
"""
import copy
import io
import json
import os
import subprocess
import tempfile

import call_mode
import diligence_flags as DF
import equity_state as E

HERE = os.path.dirname(os.path.abspath(__file__))
F = []


def chk(name, ok, d=''):
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + str(d)[:200] if d else ''))
    if not ok:
        F.append(name)


PRICED = {'conf': 'ok', 'nrec': 9, 'second_fc': [],
          'liens': [{'d': '03/04/2015', 'amt': 250000, 'st': 'OPEN', 'party': 'INVENTED BANK'}]}


def lp_row(value=400000, debt=250000, junior=0, **kw):
    # Shaped like production: on a LENDER case broward_liens / records_liens wipe the foreclosing
    # first, so orsurv / orjunior hold the juniors only and orsurvfirst is never set (Codex P1 on #467).
    liens = [{'d': '03/04/2015', 'amt': debt, 'st': 'OPEN', 'src': 'official records'}]
    if junior:
        liens.append({'d': '06/07/2018', 'amt': junior, 'st': 'OPEN', 'src': 'official records'})
    r = {'case': '2099-000001-CA-01', 'county': 'BROWARD', 'st': 'LP', 'ctype': 'Bank/Mortgage',
         'value': value, 'judg': 0, 'orliens': liens, 'orsurvsen': 0, 'orconf': 'ok'}
    if junior:
        r['orsurv'] = r['orjunior'] = junior
    r.update(kw)
    chain = copy.deepcopy(PRICED)
    chain['liens'] = [dict(l, party='INVENTED BANK') for l in r['orliens'] if isinstance(l, dict)]
    E.apply(r, chain)
    return r


# ---- the card
c = E.card(lp_row())
chk('LP lender + priced chain: recorded tier', c['recorded'] is True and c['state'] == 'priced', c)
chk('recorded tier never quotable', c['may_quote'] is False and c['net'] is None, c)
chk('recorded debt and net', c['recorded_debt'] == 250000.0 and c['recorded_net'] == 150000.0, c)
chk('why says recorded debt only, not a payoff, no figure quoted',
    'RECORDED DEBT ONLY' in c['why'] and 'not a payoff' in c['why'] and 'no figure may be quoted' in c['why'], c['why'])
chk('no em dash in the new wording', '—' not in c['why'].split('priced', 1)[-1], c['why'])

chk('auction row (FC) with a judgment is unchanged: quotable, not recorded',
    (lambda x: x['may_quote'] and not x['recorded'] and x['recorded_net'] is None)(
        E.card(lp_row(st='FC', judg=200000))))
chk('HOA filed case is not recorded (the association claim is unknown)',
    not E.card(lp_row(ctype='HOA/Condo'))['recorded'])
chk('tax deed is never recorded', not E.card(lp_row(st='TD'))['recorded'])
chk('no value: not recorded', not E.card(lp_row(value=0))['recorded'])
chk('an open lien with no amount: not recorded',
    not E.card(lp_row(orliens=[{'amt': 250000, 'st': 'OPEN'}, {'amt': None, 'st': 'OPEN'}]))['recorded'])

# ---- the foreclosing loan is in the recorded debt (Codex P1 on #467)
one = E.card(lp_row(value=400000, debt=250000))
chk('single-mortgage filed case: the foreclosing loan is the recorded debt',
    one['recorded'] and one['recorded_debt'] == 250000.0 and one['recorded_net'] == 150000.0, one)
two = E.card(lp_row(value=400000, debt=450000, junior=50000))
chk('$450k first + $50k second on $400k: recorded debt $500k, net -$100k (was +$350k)',
    two['recorded'] and two['recorded_debt'] == 500000.0 and two['recorded_net'] == -100000.0, two)
chk('that row stays out of the Has equity lane (no positive net on the handset)',
    call_mode._eqc_slim(two).get('rn') == -100000.0)
_sr = lp_row()
_sr['orliens'] = [{'amt': 250000, 'st': 'OPEN'}, {'amt': None, 'st': 'SATISFIED'},
                  {'amt': 90000, 'st': 'SATISFIED'}]
sat = E.card(_sr)
chk('satisfied liens are not debt, and an unpriced satisfied one does not block the tier',
    sat['recorded'] and sat['recorded_debt'] == 250000.0, sat)
chk('no open lien at all: not recorded',
    not E.card(lp_row(orliens=[{'amt': 250000, 'st': 'SATISFIED'}]))['recorded'])
un = copy.deepcopy(PRICED)
un['liens'][0]['amt'] = None
r = {'case': 'X', 'st': 'LP', 'ctype': 'Bank/Mortgage', 'value': 400000, 'judg': 0, 'orliens': [{'amt': None}]}
E.apply(r, un)
chk('unpriced chain: not recorded', r['eqstate'] == 'unpriced' and not E.card(r)['recorded'])
chk('unchecked lead: not recorded', not E.card({'st': 'LP', 'ctype': 'Bank/Mortgage', 'value': 400000})['recorded'])
neg = E.card(lp_row(value=200000, debt=300000))
chk('recorded debt above value: recorded, negative net', neg['recorded'] and neg['recorded_net'] == -100000.0, neg)

# ---- handset slim
s = call_mode._eqc_slim(c)
chk('slim carries r and rn, q stays 0, no n', s.get('r') == 1 and s.get('rn') == 150000.0 and s.get('q') == 0
    and 'n' not in s, s)
s2 = call_mode._eqc_slim(E.card(lp_row(st='FC', judg=200000)))
chk('quotable card: no r / rn on the handset', 'r' not in s2 and 'rn' not in s2 and s2.get('q') == 1, s2)

# ---- UNDERWATER and every other diligence flag are untouched by the card
for name, row in (('positive', lp_row()), ('underwater', lp_row(value=200000, debt=300000, junior=300000))):
    bare = {k: v for k, v in row.items() if k != 'eqc'}
    before = json.dumps(DF.risk_flags(copy.deepcopy(bare)), sort_keys=True, default=str)
    baked = dict(bare, eqc=E.card(bare))
    after = json.dumps(DF.risk_flags(copy.deepcopy(baked)), sort_keys=True, default=str)
    chk('diligence flags identical with and without the card (%s)' % name, before == after)
# UNDERWATER reads known_debt_of(), which on a lender filed case sees the juniors only (the same
# gap Codex found in the first cut of this tier). Not changed here: it is a hold, reported on #467.
# Juniors alone above the county bar still fire it, with or without the card.
uw = [f['code'] for f in DF.risk_flags(lp_row(value=200000, debt=300000, junior=300000))]
chk('a filed case whose juniors alone swallow the value still carries UNDERWATER', 'UNDERWATER' in uw, uw)

# ---- the handset text (node)
SRC = io.open(os.path.join(HERE, 'call_mode.py'), encoding='utf-8').read()


def grab_fn(name):
    i = SRC.find('function ' + name + '(')
    j = SRC.find('\n}', i)
    chk('function present: ' + name, i >= 0 and j > 0)
    return SRC[i:j + 2] + '\n'


JS = ('function esc(s){ return String(s == null ? "" : s); }\nfunction isBalloon(r){ return false; }\n'
      + ''.join(grab_fn(n) for n in ('isEquity', 'isRecordedEquity', 'eqFact', 'eqMay', '_eqUsd', 'eqCardHtml'))
      + 'var R = {eqc: ' + json.dumps(s) + '};\n'
      + 'var Q = {eqc: ' + json.dumps(s2) + '};\n'
      + 'console.log(JSON.stringify({card: eqCardHtml(R), lane: isEquity(R), qcard: eqCardHtml(Q)}));\n')
fd, path = tempfile.mkstemp(suffix='.js')
os.close(fd)
io.open(path, 'w', encoding='utf-8').write(JS)
try:
    p = subprocess.run([os.environ.get('NODE_BIN', 'node'), path], capture_output=True, text=True, timeout=60)
finally:
    os.remove(path)
chk('node ran', p.returncode == 0, p.stderr[-300:])
out = json.loads(p.stdout.strip().splitlines()[-1]) if p.returncode == 0 else {}
card = out.get('card', '')
chk('handset card: recorded debt only, not a payoff, do not quote', 'RECORDED debt only, not a payoff' in card
    and 'Do not quote a number' in card, card)
chk('handset card never prints "Net equity" for the recorded tier', 'Net equity' not in card, card)
chk('recorded row is in the Has equity lane', out.get('lane') is True)
chk('quotable card unchanged: still prints Net equity', 'Net equity' in out.get('qcard', ''))
chk('Money line has a recorded-debt branch that says do not quote',
    'Liens verified, recorded debt only (not a payoff) -- do not quote a number' in SRC)

print('FAIL: %s' % F if F else 'ALL PASS')
raise SystemExit(1 if F else 0)
