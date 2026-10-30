"""Tax-deed opening bid is never a quotable judgment; listing amounts are not labelled 'docket'. Invented rows."""
import equity_state as E

F = []
def chk(name, ok, d=''):
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + d if d else ''))
    if not ok:
        F.append(name)

CLEAR = {'conf': 'ok', 'liens': [], 'nrec': 12, 'second_fc': []}

def row(**kw):
    r = {'case': '2099-000001-CA-01', 'county': 'MIAMI-DADE', 'ctype': 'HOA/Condo', 'value': 400000,
         'judg': 100000, 'ju': False}
    r.update(kw)
    E.apply(r, CLEAR)
    return r

# tax deed, slim names (st) and raw names (sale_type / ctype); the bake stores the opening bid as judg
for name, kw in (('st=TD', {'st': 'TD'}), ('ctype Tax Deed', {'ctype': 'Tax Deed'}),
                 ('sale_type TD', {'sale_type': 'TD'}), ('Auction Type TAXDEED', {'Auction Type': 'TAXDEED'})):
    c = E.card(row(**kw))
    chk('tax deed (%s): fact but never quotable' % name, c['fact'] and not c['may_quote'] and c['net'] is None, str(c))
    chk('tax deed (%s): labelled opening bid' % name, c['judgment_source'] == 'opening bid')

# mortgage foreclosure with an auction-page figure
c = E.card(row())
chk('auction figure labelled auction listing, still quotable as before', c['judgment_source'] == 'auction listing'
    and c['may_quote'] and c['judgment'] == 100000, str(c))
chk('never labelled docket without a docket marker', c['judgment_source'] != 'docket')

# real court copy unchanged
c = E.card(row(judg_src='court copy', judgc=123456.0))
chk('court copy unchanged', c['judgment_source'] == 'court copy' and c['judgment'] == 123456.0 and c['may_quote'], str(c))
# a row that says docket keeps docket
c = E.card(row(judg_src='docket'))
chk('explicit docket marker keeps docket', c['judgment_source'] == 'docket' and c['may_quote'])
# not posted unchanged
c = E.card(row(judg=0, ju=True, st='TD'))
chk('not posted unchanged', c['judgment_source'] == 'not posted' and not c['may_quote'])
# stricter only: non-fact stays unquotable
r = {'st': 'TD', 'judg': 5000, 'value': 100000}
E.apply(r, None)
chk('unchecked tax deed not quotable', not E.card(r)['may_quote'])

# call_mode slim keeps the full words
import call_mode as CM
for src in ('auction listing', 'opening bid', 'court copy'):
    s = CM._eqc_slim({'state': 'clear', 'judgment_source': src, 'fact': True})
    chk('slim keeps "%s"' % src, s['js'] == src, str(s))

print('FAILED: %s' % F if F else 'ALL PASS')
raise SystemExit(1 if F else 0)
