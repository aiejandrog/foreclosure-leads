"""records_liens folio-keyed search (DEALFLOW_OR_FOLIO). Recorded-shape fixtures with invented people; the
network is stubbed and nothing is spent."""
import os, sys, tempfile
os.environ.setdefault('DEALFLOW_DIR', tempfile.mkdtemp())
import records_liens as R

F = []
def chk(name, ok, d=''):
    print(('  PASS ' if ok else '  FAIL ') + name + (' | ' + d if d else ''))
    if not ok:
        F.append(name)

FOL_A, FOL_B = '30-1234-001-0010', '30-1234-001-0020'
SUB = 'INVENTED SUNSET ESTATES'

def rec(doc, folio, book, page, first, second, intangible=0, when='03/04/2015', sub=SUB):
    return {'doC_TYPE': doc, 'foliO_NUMBER': folio, 'subdiV_NAME': sub, 'reC_DATE': when,
            'reC_BOOK': str(book), 'reC_PAGE': str(page), 'reC_BOOKPAGE': '%s/%s' % (book, page),
            'firsT_PARTY': first, 'seconD_PARTY': second, 'intangible': intangible, 'consideratioN_1': 0}

DEED_A = rec('DEED', FOL_A, 100, 1, 'INVENTED SELLER', 'GARCIA JOSE', when='01/02/2010')
MTG_A = rec('MORTGAGE', '', 200, 5, 'GARCIA JOSE', 'INVENTED BANK ONE', intangible=300.0)          # $150,000, folio blank
MTG_B = rec('MORTGAGE', FOL_B, 300, 9, 'GARCIA JOSE', 'INVENTED BANK TWO', intangible=900.0,    # $450,000, ANOTHER parcel
            when='06/07/2018')
NAME_SEARCH = [DEED_A, MTG_A, MTG_B]          # surname search for a common name returns both parcels' loans
FOLIO_SEARCH_A = [DEED_A]                     # the folio search returns what is indexed on A

def an(models):
    return R.analyze(models, FOL_A, 0, ftype='MORTGAGE', owner='JOSE GARCIA', case='2099-000001-CA-01')

# 1) the bug the flag fixes: unscoped, the namesake's loan on parcel B rides in on the subdivision
base = an(NAME_SEARCH)
chk('baseline pulls the other parcel\'s mortgage', base['open_count'] == 2, 'open_count=%s' % base['open_count'])

# 2) scoped: only A's instruments
scoped = R.folio_scope(NAME_SEARCH, FOL_A)
chk('scope drops a different-folio record, keeps blank + same-folio', len(scoped) == 2 and MTG_B not in scoped)
res, only = R.folio_merge(NAME_SEARCH, FOLIO_SEARCH_A, FOL_A)
chk('merge: not folio-only, deduped', not only and len(res) == 2, str(len(res)))
a2 = an(res)
chk('common-name owner no longer pulls another parcel\'s mortgage',
    a2['open_count'] == 1 and [l['amt'] for l in a2['liens']] == [150000] and 'INVENTED BANK TWO' not in str(a2['liens']), str(a2['liens'])[:120])

# 3) folio search finds an instrument the name search missed
extra = rec('MORTGAGE', FOL_A, 250, 7, 'SOMEONE ELSE', 'INVENTED BANK THREE', intangible=100.0)
res, only = R.folio_merge([DEED_A, MTG_A], [DEED_A, extra], FOL_A)
chk('folio search adds an on-parcel instrument the name search missed', len(res) == 3 and not only)

# 4) name search found nothing: folio result stands alone and says so
res, only = R.folio_merge(None, [DEED_A, MTG_B], FOL_A)
chk('folio-only result flagged, other parcel excluded', only and res == [DEED_A])
res, only = R.folio_merge(None, [MTG_B], FOL_A)
chk('a folio search with nothing on this folio is nothing', res is None and not only)

# 5) fetch_by_folio: stubbed paid fetch, result must carry the folio; cached token reused
calls = []
def stub(f):
    calls.append(f); return [DEED_A, MTG_B]
got = R.fetch_by_folio(FOL_A, {}, fetch=stub)
chk('fetch_by_folio keeps only instruments on the folio', got == [DEED_A] and calls == [R.norm_folio(FOL_A)], str(calls))
chk('a result that does not carry the folio is not believed', R.fetch_by_folio(FOL_A, {}, fetch=lambda f: [MTG_B]) is None)
chk('no folio -> no search', R.fetch_by_folio('', {}, fetch=stub) is None and len(calls) == 1)

# 6) flag default OFF
os.environ.pop('DEALFLOW_OR_FOLIO', None)
chk('flag defaults off', not R.folio_enabled())
os.environ['DEALFLOW_OR_FOLIO'] = '1'
chk('flag on with =1', R.folio_enabled())
os.environ['DEALFLOW_OR_FOLIO'] = 'true'
chk('only =1 turns it on', not R.folio_enabled())
os.environ.pop('DEALFLOW_OR_FOLIO', None)

# 7) a folio-only chain can never be a FACT
import equity_state as E
r = {'case': 'X', 'ctype': 'HOA/Condo'}
E.apply(r, dict(an([DEED_A, MTG_A]), folio_only=True, truncated=True))
chk('folio_only priced chain is not a FACT', r['eqstate'] == 'none', r['eqstate'])

# 8) off = byte-identical request: default searchtype is still Name/Document, folio type only when asked
import captcha_solver, paid_reads
urls = []
class Resp:
    def json(self): return {'qs': None}
R.S.post = lambda url, **kw: (urls.append(url), Resp())[1]
R._may_submit = lambda: True
R._ledger_save = lambda *a, **k: None
paid_reads.debit = lambda *a, **k: (True, '')
captcha_solver.solve_turnstile = lambda *a, **k: 'tok'
R.time.sleep = lambda *_: None
R.fetch_via_turnstile(('GARCIA', 'JOSE'), tries=1)
R.fetch_via_turnstile(('301234001001', ''), tries=1, searchtype='Parcel ID')
chk('default search URL unchanged', urls[0].endswith('searchtype=Name/Document') and 'partyName=GARCIA&' in urls[0], urls[0][-90:])
chk('folio search uses the folio searchtype', urls[1].endswith('searchtype=Parcel%20ID') and 'partyName=301234001001&' in urls[1], urls[1][-90:])

print('FAIL: %s' % F if F else 'ALL PASS')
sys.exit(1 if F else 0)
