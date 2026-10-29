"""equity_state.card(): the five states, the judgment gate, the lender-foreclosure demotion. Invented fixtures."""
import equity_state as E

F = []
def chk(name, ok, d=''):
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + d if d else ''))
    if not ok:
        F.append(name)

KEYS = {'state', 'label', 'fact', 'judgment', 'judgment_source', 'liens', 'basis', 'basis_source',
        'net', 'may_quote', 'why'}

def row(chain, **kw):
    r = {'case': '2099-000001-CA-01', 'county': 'MIAMI-DADE', 'ctype': 'HOA/Condo', 'value': 400000,
         'judg': 100000, 'ju': False}
    r.update(kw)
    E.apply(r, chain)
    return r

OK_CLEAR = {'conf': 'ok', 'liens': [], 'nrec': 12, 'second_fc': []}
PRICED = {'conf': 'ok', 'nrec': 9, 'liens': [{'d': '03/04/2015', 'amt': 150000, 'st': 'OPEN', 'party': 'INVENTED BANK'}]}
UNPRICED = {'conf': 'ok', 'nrec': 9, 'liens': [{'d': '03/04/2015', 'amt': None, 'st': 'OPEN'}]}
NONE = {'conf': 'none', 'liens': []}

c = E.card(row(OK_CLEAR))
chk('schema keys', set(c) == KEYS, str(sorted(set(c) ^ KEYS)))
chk('clear: fact + may_quote + net', c['state'] == 'clear' and c['fact'] and c['may_quote'] and c['net'] == 300000.0, str(c))
chk('judgment source docket', c['judgment'] == 100000 and c['judgment_source'] == 'docket')
chk('basis county', c['basis'] == 400000 and c['basis_source'] == 'county')

c = E.card(row(PRICED, orliens=PRICED['liens'], orsurvfirst=150000))
chk('priced: fact, lien listed, recorded not payoff', c['state'] == 'priced' and c['may_quote']
    and c['liens'] == [{'type': 'mortgage', 'amount': 150000, 'recorded': '03/04/2015', 'source': 'official records'}], str(c['liens']))
chk('priced net uses judgment + chain, a direction', c['net'] == 150000.0, str(c['net']))

c = E.card(row(UNPRICED))
chk('unpriced: not fact, not quotable, no net', c['state'] == 'unpriced' and not c['fact'] and not c['may_quote'] and c['net'] is None)
c = E.card(row(NONE))
chk('none: guess', c['state'] == 'none' and not c['fact'] and not c['may_quote'])
c = E.card(row(None))
chk('unchecked: guess', c['state'] == 'unchecked' and not c['may_quote'])
c = E.card({'value': 100})
chk('never-stamped lead reads unchecked', c['state'] == 'unchecked' and set(c) == KEYS)
chk('garbage in, no raise', E.card(None)['state'] == 'unchecked' and E.card({'judg': float('nan')})['judgment'] is None)

# fact but judgment not posted -> never quotable
c = E.card(row(OK_CLEAR, judg=0, ju=True))
chk('clear + judgment not posted: fact but may_quote False', c['fact'] and not c['may_quote'] and c['judgment'] is None
    and c['judgment_source'] == 'not posted' and c['net'] is None, str(c))

# court copy provenance
c = E.card(row(OK_CLEAR, judg_src='court copy'))
chk('court copy source', c['judgment_source'] == 'court copy')

# lender foreclosure on the lead's own case: an empty "clear" is demoted to none
c = E.card(row(OK_CLEAR, ctype='Bank/Mortgage'))
chk('lender foreclosure demotes clear', c['state'] == 'none' and not c['may_quote'] and c['why'].startswith('UNVERIFIED'), c['why'])
# separate lender case found later (demote_for_bank_fc)
r = row(OK_CLEAR, orsecond={'case': '2099-000002-CA-01', 'party': 'INVENTED BANK NA'})
chk('second-case demotion applied', E.demote_for_bank_fc(r))
c = E.card(r)
chk('card reads demoted state', c['state'] == 'none' and not c['may_quote'] and 'separate case' in c['why'], c['why'])

print('FAIL: %s' % F if F else 'ALL PASS')
raise SystemExit(1 if F else 0)
